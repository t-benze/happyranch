import Foundation

// MARK: - DaemonCredentialProvider protocol

/// Pluggable seam for injecting the daemon credential on the loopback hop.
///
/// The home connector is the **sole custodian** of the daemon credential:
/// it reads the token **locally, out-of-band**, and injects it only on the
/// loopback hop to `127.0.0.1:<daemon_port>`.  The token **never crosses
/// the tailnet**.
///
/// v1 implementation = ``LocalTokenCredentialProvider`` (reads `daemon.token`
/// from the daemon home directory and returns the master bearer).
///
/// Future: a scoped / per-session token (modeled on the `hrreg_` short-TTL
/// scoped-token precedent) can be provided via a different implementation
/// **with no connector rewrite or daemon auth-code change**.
public protocol DaemonCredentialProvider: AnyObject, Sendable {
    /// Return the credential value to inject as the HTTP Authorization header
    /// on the loopback hop.
    ///
    /// - Throws: If the credential cannot be read or is unavailable.
    /// - Returns: The credential string (without the "Bearer " prefix —
    ///   the connector prepends it).
    func credential() throws -> String
}

// MARK: - LocalTokenCredentialProvider (v1)

/// v1 implementation of ``DaemonCredentialProvider``.
///
/// Reads `daemon.token` from the daemon home directory and returns
/// the master bearer token.  This is the **master** token — the same
/// one the CLI uses for loopback calls.
///
/// The credential is read on each call so that a daemon restart
/// (which issues a fresh token) is handled without restarting
/// the connector.
public final class LocalTokenCredentialProvider: DaemonCredentialProvider, @unchecked Sendable {

    /// Path to the daemon home directory.
    private let homeDir: String

    /// File manager used for reading the token file (injectable for tests).
    private let fileManager: FileManager

    /// Path to the daemon.token file, derived from `homeDir`.
    private var tokenFilePath: String {
        (homeDir as NSString).appendingPathComponent("daemon.token")
    }

    // MARK: - Init

    /// - Parameters:
    ///   - homeDir: Path to the daemon home directory (e.g. `~/.happyranch/`).
    ///   - fileManager: FileManager used for reading (injectable for tests).
    public init(
        homeDir: String,
        fileManager: FileManager = .default
    ) {
        self.homeDir = homeDir
        self.fileManager = fileManager
    }

    // MARK: - DaemonCredentialProvider

    public func credential() throws -> String {
        guard fileManager.fileExists(atPath: tokenFilePath) else {
            throw DaemonCredentialProviderError.tokenFileNotFound(path: tokenFilePath)
        }

        let content: String
        do {
            content = try String(contentsOfFile: tokenFilePath, encoding: .utf8)
        } catch {
            throw DaemonCredentialProviderError.tokenFileUnreadable(
                path: tokenFilePath,
                underlying: error.localizedDescription
            )
        }

        let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw DaemonCredentialProviderError.tokenFileEmpty(path: tokenFilePath)
        }

        return trimmed
    }
}

// MARK: - Errors

public enum DaemonCredentialProviderError: Error, Equatable {
    /// The `daemon.token` file does not exist at the expected path.
    case tokenFileNotFound(path: String)

    /// The `daemon.token` file could not be read.
    case tokenFileUnreadable(path: String, underlying: String)

    /// The `daemon.token` file exists but is empty.
    case tokenFileEmpty(path: String)
}

// Make the `.tokenFileUnreadable` case Equatable despite the underlying Error.
extension DaemonCredentialProviderError {
    public static func == (lhs: DaemonCredentialProviderError, rhs: DaemonCredentialProviderError) -> Bool {
        switch (lhs, rhs) {
        case (.tokenFileNotFound(let lp), .tokenFileNotFound(let rp)):
            return lp == rp
        case (.tokenFileUnreadable(let lp, let lu), .tokenFileUnreadable(let rp, let ru)):
            return lp == rp && lu == ru
        case (.tokenFileEmpty(let lp), .tokenFileEmpty(let rp)):
            return lp == rp
        default:
            return false
        }
    }
}
