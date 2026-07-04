import Foundation
import Testing
@testable import HappyRanchApp
import HappyRanchSupervisor

@Suite("ProcessHandle stream capture - FakeProcessHandle")
struct FakeProcessHandleCaptureTests {

    @Test("FakeProcessHandle stores captured stderr from simulateCrash")
    func fakeHandleStoresCapturedStderr() {
        let handle = FakeProcessHandle()
        handle.simulateCrash(exitCode: 1, stderr: "Fatal error: port already in use\n")
        #expect(handle.capturedStandardError == "Fatal error: port already in use\n")
        #expect(handle.capturedStandardOutput == nil)
    }

    @Test("FakeProcessHandle stores captured stdout and stderr")
    func fakeHandleStoresCapturedStdoutAndStderr() {
        let handle = FakeProcessHandle()
        handle.simulateCrash(exitCode: 1, stderr: "error: bind failed\n", stdout: "daemon starting...\n")
        #expect(handle.capturedStandardError == "error: bind failed\n")
        #expect(handle.capturedStandardOutput == "daemon starting...\n")
    }

    @Test("FakeProcessHandle per-launch invariant: each handle carries its own streams")
    func perLaunchInvariant() {
        let handleA = FakeProcessHandle(processIdentifier: 100)
        let handleB = FakeProcessHandle(processIdentifier: 200)

        handleA.simulateCrash(exitCode: 1, stderr: "error from A")
        handleB.simulateCrash(exitCode: 2, stderr: "error from B")

        #expect(handleA.capturedStandardError == "error from A")
        #expect(handleA.terminationStatus == 1)
        #expect(handleB.capturedStandardError == "error from B")
        #expect(handleB.terminationStatus == 2)
    }

    // MARK: - RealProcessController stream capture

    @Test("RealProcessController wires stdout and stderr pipes for real process")
    func realControllerWiresPipes() throws {
        let controller = RealProcessController()
        let handle = try controller.launch(
            executableURL: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "echo stdout-text; echo stderr-text >&2; exit 0"],
            currentDirectoryURL: nil,
            environment: nil,
            terminationHandler: nil
        )
        let proc = handle as! RealProcessHandle
        // Poll for exit
        let deadline = Date().addingTimeInterval(5.0)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        let stdout = proc.capturedStandardOutput ?? ""
        let stderr = proc.capturedStandardError ?? ""
        #expect(stdout.contains("stdout-text"), "stdout should contain 'stdout-text', got: \(stdout)")
        #expect(stderr.contains("stderr-text"), "stderr should contain 'stderr-text', got: \(stderr)")
    }
}
