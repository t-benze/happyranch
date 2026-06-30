import Foundation
import HappyRanchSupervisor

/// Test double for ProcessControlling that records calls and allows
/// test-driven control of isRunning / process state.
final class FakeProcessController: ProcessControlling, @unchecked Sendable {
    var isRunning: Bool = false
    var processIdentifier: Int32 = 0
    var terminationStatus: Int32 = 0
    var terminationReason: Process.TerminationReason = .exit
    var terminationHandler: (@Sendable (any ProcessControlling) -> Void)?

    // Call counters for verification
    var launchCallCount = 0
    var terminateCallCount = 0

    // Last-launch arguments for verification
    var lastExecutableURL: URL?
    var lastArguments: [String]?
    var lastCurrentDirectoryURL: URL?
    var lastEnvironment: [String: String]?

    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?
    ) throws {
        launchCallCount += 1
        lastExecutableURL = executableURL
        lastArguments = arguments
        lastCurrentDirectoryURL = currentDirectoryURL
        lastEnvironment = environment
        isRunning = true
        processIdentifier = 12345
    }

    func terminate() {
        terminateCallCount += 1
        isRunning = false
    }
}
