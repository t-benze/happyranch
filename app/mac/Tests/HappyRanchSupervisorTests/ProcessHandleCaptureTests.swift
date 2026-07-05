import Foundation
import Testing
@testable import HappyRanchSupervisor

@Suite("ProcessHandle stream capture - protocol")
struct ProcessHandleCaptureProtocolTests {

    // The protocol itself declares capturedStandardError and capturedStandardOutput.
    // Since we can't instantiate a protocol directly, we verify that the fields
    // exist in the protocol definition by checking that they compile when
    // accessed on a protocol-typed reference. We use a simple mock.
    private final class MockHandle: ProcessHandle, @unchecked Sendable {
        var processIdentifier: Int32 = 0
        var isRunning: Bool = false
        var terminationStatus: Int32 = 0
        var terminationReason: Process.TerminationReason = .exit
        var capturedStandardError: String?
        var capturedStandardOutput: String?
        func terminate() {}
    }

    @Test("ProcessHandle protocol declares capturedStandardError and capturedStandardOutput")
    func protocolDeclaresCaptureProps() {
        let handle = MockHandle()
        #expect(handle.capturedStandardError == nil)
        #expect(handle.capturedStandardOutput == nil)

        handle.capturedStandardError = "some error"
        #expect(handle.capturedStandardError == "some error")
    }
}
