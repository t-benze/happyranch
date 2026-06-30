import Foundation
import HappyRanchSupervisor

/// Error thrown by FakeProcessController when a single-use constraint is violated.
enum FakeProcessControllerError: Error, Equatable {
    case processAlreadyExited
}

/// Test double for ProcessControlling that records calls and allows
/// test-driven control of isRunning / process state.
///
/// Models Foundation.Process single-use semantics: `launch()` throws
/// `processAlreadyExited` if called again after the underlying process
/// has exited (via `terminate()` or `simulateCrash()`).
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

    /// Tracks whether the underlying process has exited.
    /// Once true, subsequent `launch()` calls throw `processAlreadyExited`.
    private var hasExited = false

    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?
    ) throws {
        if hasExited {
            throw FakeProcessControllerError.processAlreadyExited
        }
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
        hasExited = true
        terminationReason = .exit
        terminationStatus = 0
        // Fire termination handler so AppDelegate processes the exit
        terminationHandler?(self)
    }

    /// Simulate a crash exit without going through `terminate()`.
    /// Fires the termination handler so AppDelegate transitions to .crashed.
    func simulateCrash(exitCode: Int32) {
        isRunning = false
        hasExited = true
        terminationReason = .uncaughtSignal
        terminationStatus = exitCode
        terminationHandler?(self)
    }
}
