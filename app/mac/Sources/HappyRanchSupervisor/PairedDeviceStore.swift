import Foundation

// MARK: - PairedDeviceStore protocol

/// Injectable seam for checking whether a remote device is authorized
/// to connect to the home connector, and for managing per-device pairing
/// credentials.
///
/// ## Pairing flow (A2.3)
///
/// 1. HOME side calls ``generatePairingCode()`` — gets a one-time
///    short code to display to the user (e.g. in the home app UI).
/// 2. CLIENT side sends `POST /pair` with the code to the home connector.
/// 3. The home connector calls ``pair(usingCode:deviceName:)`` — the
///    store validates the code, creates a per-device credential (prefixed
///    `hrpair_`), and stores it.
/// 4. The client receives the credential and sends it as the
///    `X-HappyRanch-Device-Credential` header on subsequent requests.
/// 5. The home connector calls ``isPaired(deviceID:)`` with that header
///    value — the store checks the credential is registered.
///
/// ## Credential properties
/// - Prefixed `hrpair_` — visually distinct from `hr_token_` (daemon master)
///   and `hr_session_` (client-bridge session-scoped).
/// - Home-side-verifiable — the home connector checks a locally-stored set;
///   no crypto signatures cross the tailnet.
/// - Does NOT leak the daemon token — the pairing credential is a separate
///   random value, never derived from the daemon master.
/// - Individually revocable — ``revokePairing(credential:)`` removes one
///   credential without affecting others (A2.4 acts on this).
public protocol PairedDeviceStore: AnyObject, Sendable {
    /// Check whether the given device credential is authorized.
    ///
    /// - Parameter deviceID: The pairing credential from the
    ///   `X-HappyRanch-Device-Credential` request header.
    /// - Returns: `true` if the device is paired and authorized.
    func isPaired(deviceID: String) -> Bool

    /// Generate a one-time pairing code that the home-side app displays
    /// to the user for entry on the client side.
    ///
    /// The code is stored internally with a short TTL (5 minutes).
    /// Each call replaces any previously outstanding code.
    func generatePairingCode() -> String

    /// Attempt to pair a remote device using a pairing code.
    ///
    /// Validates the code against the stored one-time code.  If valid:
    /// - Consumes the code (one-time use).
    /// - Generates a per-device `hrpair_` credential.
    /// - Stores the credential + device name.
    /// - Returns the credential for the client to store.
    ///
    /// - Parameters:
    ///   - usingCode: The pairing code entered by the user on the client side.
    ///   - deviceName: A human-readable name for the device (for UI display).
    /// - Returns: The per-device credential, or `nil` if the code is invalid
    ///   or expired.
    func pair(usingCode: String, deviceName: String) -> String?

    /// Revoke a paired device credential.
    ///
    /// After revocation, ``isPaired(deviceID:)`` returns `false` for this
    /// credential, and any in-flight request carrying it will be rejected.
    /// A2.4 acts on this to tear down live sessions.
    ///
    /// - Parameter credential: The `hrpair_` credential to revoke.
    /// - Returns: `true` if a device was revoked, `false` if the credential
    ///   was not found.
    func revokePairing(credential: String) -> Bool
}

// MARK: - StubPairedDeviceStore (test-only)

/// Test stub: **denies all devices by default**.
///
/// Tests MUST explicitly configure accepted credentials via
/// ``alwaysAllow(_:)`` or by injecting their own conforming type.
/// A random tailnet peer with NO credential or an invalid credential
/// is ALWAYS rejected — the permissive A2.1 default was a CRITICAL
/// bypass (reviewer FINDING 3).
///
/// For production, use ``RealPairingStore`` (A2.3).
public final class StubPairedDeviceStore: PairedDeviceStore, @unchecked Sendable {

    /// Set of credentials that are considered paired.
    /// Empty by default — caller must explicitly add credentials.
    private var allowedCredentials: Set<String> = []

    /// Whether to allow all credentials (test convenience).
    /// When false (default), only credentials in ``allowedCredentials`` pass.
    private var allowAll = false

    public init() {}

    /// Configure a specific credential to be accepted.
    public func allowCredential(_ credential: String) {
        allowedCredentials.insert(credential)
    }

    /// Opt-in to permissive mode (tests that need the old A2.1 behavior).
    public func setAllowAll(_ allow: Bool) {
        allowAll = allow
    }

    public func isPaired(deviceID: String) -> Bool {
        if allowAll { return true }
        guard !deviceID.isEmpty else { return false }
        return allowedCredentials.contains(deviceID)
    }

    public func generatePairingCode() -> String {
        return "STUB-CODE"
    }

    public func pair(usingCode: String, deviceName: String) -> String? {
        let credential = "stub-credential-\(UUID().uuidString.prefix(8))"
        allowedCredentials.insert(credential)
        return credential
    }

    public func revokePairing(credential: String) -> Bool {
        if allowedCredentials.contains(credential) {
            allowedCredentials.remove(credential)
            return true
        }
        return false
    }
}
