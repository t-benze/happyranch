import Foundation

// MARK: - RealPairingStore

/// The REAL per-device pairing-credential store (A2.3).
///
/// Replaces the A2.1 ``StubPairedDeviceStore`` with a proper, thread-safe
/// implementation that:
///
/// - Generates one-time pairing codes (short alphanumeric, 5-min TTL).
/// - Validates codes and issues per-device `hrpair_` credentials.
/// - Checks credentials on every proxied request via ``isPaired(deviceID:)``.
/// - Supports individual credential revocation (A2.4).
///
/// ## Thread safety
/// All mutable state is guarded by an `NSLock`.  The lock is held only
/// for the duration of each mutation or read — no I/O under the lock.
///
/// ## Design invariants
/// - The credential is a random hex string prefixed `hrpair_` — visually
///   distinct from `hr_token_` (daemon master) and `hr_session_`
///   (client-bridge session).  The daemon token is **never** derivable
///   from it.
/// - The store is home-side: the home connector checks a locally-stored
///   set; no crypto signatures travel the tailnet.
/// - Each device gets its own credential; revoking one does not affect
///   others.
public final class RealPairingStore: PairedDeviceStore, @unchecked Sendable {

    // MARK: - Private state

    private let lock = NSLock()

    /// The currently-active one-time pairing code and its expiry.
    private var activePairingCode: String?
    private var activePairingCodeExpiry: Date = .distantPast

    /// Paired device credentials → device name.
    private var pairedDevices: [String: String] = [:]

    /// Revoked credentials (kept so they can't re-pair with the same credential).
    private var revokedCredentials: Set<String> = []

    /// TTL for pairing codes (in seconds).
    private static let pairingCodeTTL: TimeInterval = 300  // 5 minutes

    /// Prefix for per-device credentials.
    private static let credentialPrefix = "hrpair_"

    /// Length of the random part of the pairing code.
    private static let pairingCodeLength = 8

    /// Length of the random part of the device credential.
    private static let credentialRandomBytes = 16

    // MARK: - Init

    public init() {}

    // MARK: - PairedDeviceStore conformance

    public func isPaired(deviceID: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }

        // Empty credential is never paired
        guard !deviceID.isEmpty else { return false }

        // Check revoked first — revoked credentials stay rejected
        if revokedCredentials.contains(deviceID) { return false }

        return pairedDevices[deviceID] != nil
    }

    public func generatePairingCode() -> String {
        let code = Self.generateRandomCode(length: Self.pairingCodeLength)

        lock.lock()
        activePairingCode = code
        activePairingCodeExpiry = Date().addingTimeInterval(Self.pairingCodeTTL)
        lock.unlock()

        return code
    }

    public func pair(usingCode: String, deviceName: String) -> String? {
        lock.lock()

        // 1. Validate the code
        guard let storedCode = activePairingCode,
              storedCode == usingCode,
              Date() < activePairingCodeExpiry else {
            lock.unlock()
            return nil
        }

        // 2. Consume the code (one-time use)
        activePairingCode = nil
        activePairingCodeExpiry = .distantPast

        // 3. Generate a per-device credential
        let credential = Self.generateCredential()

        // 4. Store the credential
        pairedDevices[credential] = deviceName

        lock.unlock()

        return credential
    }

    public func revokePairing(credential: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }

        guard pairedDevices[credential] != nil else {
            return false
        }

        pairedDevices.removeValue(forKey: credential)
        revokedCredentials.insert(credential)
        return true
    }

    // MARK: - Additional accessors (for UI / testing)

    /// The number of currently-paired devices.
    public var pairedDeviceCount: Int {
        lock.lock()
        defer { lock.unlock() }
        return pairedDevices.count
    }

    /// The names of currently-paired devices.
    public var pairedDeviceNames: [String] {
        lock.lock()
        defer { lock.unlock() }
        return Array(pairedDevices.values)
    }

    /// Whether a pairing code is currently active (not expired).
    public var hasActivePairingCode: Bool {
        lock.lock()
        defer { lock.unlock() }
        guard activePairingCode != nil else { return false }
        return Date() < activePairingCodeExpiry
    }

    // MARK: - Private helpers

    /// Generate a random alphanumeric code of the given length.
    private static func generateRandomCode(length: Int) -> String {
        let chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  // no 0/O/1/I to avoid confusion
        return String((0..<length).map { _ in chars.randomElement()! })
    }

    /// Generate a per-device credential: `hrpair_<random hex>`.
    private static func generateCredential() -> String {
        let randomBytes = (0..<credentialRandomBytes).map { _ in UInt8.random(in: 0...255) }
        let hex = randomBytes.map { String(format: "%02x", $0) }.joined()
        return "\(credentialPrefix)\(hex)"
    }
}
