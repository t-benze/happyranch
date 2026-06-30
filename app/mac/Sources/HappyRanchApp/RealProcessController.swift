import Foundation
import HappyRanchSupervisor

/// Production implementation of ProcessControlling that wraps Foundation.Process.
/// Not Sendable-safe by construction (Process is not Sendable), but only accessed
/// from @MainActor via AppDelegate.
///
/// Foundation.Process is single-use: calling run() a second time after the
/// process has exited throws NSCocoaErrorDomain Code=3587.  This controller
/// therefore creates a fresh Process on every launch(), discarding any prior
/// instance.  The caller's terminationHandler closure is preserved across
/// launches and wired to each new Process so that AppDelegate receives exactly
/// one exit callback per managed daemon lifecycle, delivered as a
/// RealProcessHandle capturing THAT process's state.
final class RealProcessController: ProcessControlling, @unchecked Sendable {
    private var _terminationHandler: (@Sendable (any ProcessHandle) -> Void)?

    var terminationHandler: (@Sendable (any ProcessHandle) -> Void)? {
        get { _terminationHandler }
        set { _terminationHandler = newValue }
    }

    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?
    ) throws -> any ProcessHandle {
        let newProcess = Process()
        newProcess.executableURL = executableURL
        newProcess.arguments = arguments
        newProcess.currentDirectoryURL = currentDirectoryURL
        newProcess.environment = environment

        let handle = RealProcessHandle(process: newProcess)

        // Capture the handle in the Process's terminationHandler so that
        // when this specific process exits, we deliver ITS handle — not the
        // controller and not whatever the current mutable slot holds.
        newProcess.terminationHandler = { [weak self] _ in
            guard let self else { return }
            self._terminationHandler?(handle)
        }

        try newProcess.run()
        return handle
    }
}

/// Per-launch handle wrapping a Foundation.Process.
/// Delivers the wrapped Process's terminationStatus, terminationReason,
/// processIdentifier, and isRunning.
final class RealProcessHandle: ProcessHandle, @unchecked Sendable {
    private let process: Process

    init(process: Process) {
        self.process = process
    }

    var processIdentifier: Int32 { process.processIdentifier }
    var isRunning: Bool { process.isRunning }
    var terminationStatus: Int32 { process.terminationStatus }
    var terminationReason: Process.TerminationReason { process.terminationReason }

    func terminate() {
        process.terminate()
    }
}
