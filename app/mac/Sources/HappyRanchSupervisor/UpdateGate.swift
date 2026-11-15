import Foundation

/// Install policy only: no AppKit, Sparkle, network, timers or process effects.
public enum UpdateGate {
    public enum Choice: Equatable, Sendable { case whenIdle, restart, now, later }
    public enum Action: Equatable {
        case release, stopDaemon, confirmRestart, wait, later
        case prompt(runningSessions: Int?)
    }

    public static func action(
        ownsDaemon: Bool, processRunning: Bool?, runningSessions: Int?, choice: Choice? = nil
    ) -> Action {
        if choice == .later { return .later }
        guard ownsDaemon else { return .release }
        if processRunning == false { return .release }
        guard processRunning == true else { return .wait }
        guard let count = runningSessions, count >= 0 else {
            return choice == .whenIdle ? .wait : .prompt(runningSessions: nil)
        }
        if choice == .now { return .stopDaemon }
        if count == 0 { return choice == .restart ? .stopDaemon : .confirmRestart }
        return choice == .whenIdle ? .wait : .prompt(runningSessions: count)
    }

    /// A signal request or unknown process identity is never exit confirmation.
    public static func canReleaseAfterStop(sameProcess: Bool, processRunning: Bool?) -> Bool {
        sameProcess && processRunning == false
    }
}

public enum UpdateConfiguration {
    /// Same canonical base64/32-byte contract as sparkle_bundle.py.
    public static func hasValidPublicKey(_ value: Any?) -> Bool {
        guard let value = value as? String,
              let bytes = Data(base64Encoded: value), bytes.count == 32 else { return false }
        return bytes.base64EncodedString() == value
    }

    public static func needsMoveNotice(bundleURL: URL, homeURL: URL, readOnlyVolume: Bool) -> Bool {
        let path = bundleURL.standardizedFileURL.path
        let downloads = homeURL.appendingPathComponent("Downloads").standardizedFileURL.path + "/"
        return path.hasSuffix(".app") && (readOnlyVolume || path.hasPrefix(downloads)
            || path.contains("/AppTranslocation/") || path.hasPrefix("/Volumes/"))
    }
}
