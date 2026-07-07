import Foundation

// MARK: - PairedDeviceStore protocol

/// Injectable seam for checking whether a remote device is authorized
/// to connect to the home connector.
///
/// **A2.1 stub**: always returns `true` (all paired).  Phase A2.3
/// will replace the stub with a real implementation backed by a
/// per-device pairing handshake and persistent store.
///
/// The reviewer should confirm this is a clean, drop-in seam that
/// requires no connector rewrite when the real store lands.
public protocol PairedDeviceStore: AnyObject, Sendable {
    /// Check whether the given device is authorized to connect.
    ///
    /// - Parameter deviceID: An opaque device identifier from the
    ///   pairing handshake (A2.3).
    /// - Returns: `true` if the device is paired and authorized.
    func isPaired(deviceID: String) -> Bool
}

// MARK: - StubPairedDeviceStore (A2.1 only)

/// v1 stub: all devices are considered paired.
///
/// This is deliberately visible so the code_reviewer can confirm
/// the seam is clean and the real implementation (A2.3) requires
/// only conforming a new type to ``PairedDeviceStore`` — no
/// connector changes.
public final class StubPairedDeviceStore: PairedDeviceStore, @unchecked Sendable {

    public init() {}

    public func isPaired(deviceID: String) -> Bool {
        // A2.1: all devices are paired.
        // A2.3: replace with real pairing check.
        return true
    }
}
