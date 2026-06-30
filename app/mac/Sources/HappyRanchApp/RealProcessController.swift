import Foundation
import HappyRanchSupervisor

/// Production implementation of ProcessControlling that wraps Foundation.Process.
/// Not Sendable-safe by construction (Process is not Sendable), but only accessed
/// from @MainActor via AppDelegate.
final class RealProcessController: ProcessControlling, @unchecked Sendable {
    private let process = Process()

    var isRunning: Bool { process.isRunning }
    var processIdentifier: Int32 { process.processIdentifier }
    var terminationStatus: Int32 { process.terminationStatus }
    var terminationReason: Process.TerminationReason { process.terminationReason }

    var terminationHandler: (@Sendable (any ProcessControlling) -> Void)? {
        didSet {
            process.terminationHandler = { [weak self] _ in
                guard let self else { return }
                self.terminationHandler?(self)
            }
        }
    }

    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?
    ) throws {
        process.executableURL = executableURL
        process.arguments = arguments
        process.currentDirectoryURL = currentDirectoryURL
        process.environment = environment
        try process.run()
    }

    func terminate() {
        process.terminate()
    }
}
