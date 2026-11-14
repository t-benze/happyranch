import Foundation
import Darwin

/// Owns a caller's malloc/calloc allocation. PR2 supplies Keychain bytes directly;
/// byteCount includes the NUL terminator and the entire initialized allocation.
/// Ownership transfers at init. Do not access/free the allocation afterwards.
public final class ManagedEnrollmentKey: @unchecked Sendable {
    private let lock = NSLock()
    private var allocation: UnsafeMutableRawPointer?
    private let byteCount: Int

    public init(taking allocation: UnsafeMutableRawPointer, byteCount: Int) {
        precondition(byteCount > 0)
        self.allocation = allocation
        self.byteCount = byteCount
    }

    /// The native init copies synchronously. Wipe all bytes before returning,
    /// including validation errors, the empty resume key, and the terminator.
    func consume<T>(_ body: (UnsafePointer<CChar>) throws -> T) throws -> T {
        try lock.withLock {
            guard let allocation else { throw ManagedNetworkError.invalidConfiguration }
            defer { releaseAllocation() }
            let bytes = allocation.assumingMemoryBound(to: UInt8.self)
            guard bytes[byteCount - 1] == 0,
                  !UnsafeBufferPointer(start: bytes, count: byteCount - 1).contains(0) else {
                throw ManagedNetworkError.invalidConfiguration
            }
            return try body(UnsafePointer(allocation.assumingMemoryBound(to: CChar.self)))
        }
    }

    func discard() { lock.withLock { releaseAllocation() } }

    private func releaseAllocation() {
        guard let allocation else { return }
        explicit_bzero(allocation, byteCount)
        free(allocation)
        self.allocation = nil
    }

    deinit { releaseAllocation() }
}

/// Fixed ABI categories only. Never publish native/upstream error strings.
public enum ManagedNetworkError: Int32, Error, Equatable, Sendable, LocalizedError {
    case invalidConfiguration = -10
    case busy = -11
    case canceled = -12
    case deadline = -13
    case backend = -14
    case unavailable = -15
    case stateFailure = -16
    case targetNotPeer = -17
    case peerChanged = -18

    static func result(_ code: Int32) -> Self { Self(rawValue: code) ?? .backend }

    public var errorDescription: String? {
        switch self {
        case .invalidConfiguration: return "Managed network configuration or ambient enrollment override refused"
        case .busy: return "A managed network operation is still settling"
        case .canceled: return "Join canceled. Enrollment may have completed; the key may be consumed"
        case .deadline: return "Join timed out. Enrollment may have completed; the key may be consumed"
        case .backend: return "Join refused or not completed. Request a new enrollment key"
        case .unavailable: return "Managed network unavailable. Join or check saved membership first"
        case .stateFailure: return "Managed membership state could not be safely opened or saved. Enrollment may have completed; the key may be consumed"
        case .targetNotPeer: return "Managed target must be a current tailnet peer"
        case .peerChanged: return "Managed peer unavailable or changed; reconnect and pair again"
        }
    }
}
