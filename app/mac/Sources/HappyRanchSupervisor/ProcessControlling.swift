import Foundation

/// Protocol abstracting daemon child-process control so AppDelegate's
/// process lifecycle is testable without spawning real processes.
///
/// The controller is a LONG-LIVED FACTORY: each `launch()` call creates a
/// fresh underlying process and returns a fresh `ProcessHandle` representing
/// that single launch.  When the launched process exits, the
/// `terminationHandler` delivers the EXITED handle — a per-launch snapshot
/// carrying THAT process's own terminationStatus, terminationReason, and
/// processIdentifier.  A stale callback from a prior launch therefore reports
/// ITS OWN status, never the current/new process's values.
///
/// The real implementation wraps Foundation.Process per launch and delivers
/// a RealProcessHandle; tests inject a FakeProcessController that vends
/// FakeProcessHandle instances.
public protocol ProcessControlling: AnyObject, Sendable {
    /// Launch the daemon process with the given configuration.
    /// Returns a fresh ProcessHandle per call.
    @discardableResult
    func launch(
        executableURL: URL,
        arguments: [String],
        currentDirectoryURL: URL?,
        environment: [String: String]?
    ) throws -> any ProcessHandle

    /// Callback invoked when a launched process exits.
    /// Delivers the EXITED handle (per-launch snapshot), NOT the controller.
    var terminationHandler: (@Sendable (any ProcessHandle) -> Void)? { get set }
}
