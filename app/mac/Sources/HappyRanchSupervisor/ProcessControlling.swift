import Foundation

/// Protocol abstracting daemon child-process control so AppDelegate's
/// process lifecycle is testable without spawning real processes.
///
/// The real implementation wraps Foundation.Process; tests inject a
/// FakeProcessController to verify terminate/isRunning semantics.
public protocol ProcessControlling: AnyObject, Sendable {
    /// Whether the process is currently running.
    var isRunning: Bool { get }

    /// The process identifier (valid only after launch).
    var processIdentifier: Int32 { get }

    /// The exit code of a terminated process.
    var terminationStatus: Int32 { get }

    /// Why the process terminated.
    var terminationReason: Process.TerminationReason { get }

    /// Launch the process with the given configuration.
    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?
    ) throws

    /// Send SIGTERM to the process.
    func terminate()

    /// Callback invoked when the process exits.
    /// Mirrors Foundation.Process.terminationHandler.
    var terminationHandler: (@Sendable (any ProcessControlling) -> Void)? { get set }
}
