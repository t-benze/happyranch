import Foundation
import Testing
@testable import HappyRanchSupervisor

@Suite("PortReader")
struct PortReaderTests {

    @Test("reads valid port from file")
    func readsValidPort() throws {
        let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let portFile = tmpDir.appendingPathComponent("daemon.port")
        try "54321\n".write(to: portFile, atomically: true, encoding: .utf8)

        let reader = PortReader()
        let port = try reader.readPort(from: portFile)
        #expect(port == 54321)
    }

    @Test("reads port with trailing whitespace")
    func readsPortWithWhitespace() throws {
        let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let portFile = tmpDir.appendingPathComponent("daemon.port")
        try "  8888 \n  ".write(to: portFile, atomically: true, encoding: .utf8)

        let reader = PortReader()
        let port = try reader.readPort(from: portFile)
        #expect(port == 8888)
    }

    @Test("throws on empty file")
    func throwsOnEmptyFile() throws {
        let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let portFile = tmpDir.appendingPathComponent("daemon.port")
        try "".write(to: portFile, atomically: true, encoding: .utf8)

        let reader = PortReader()
        do {
            _ = try reader.readPort(from: portFile)
            Issue.record("Expected error for empty file")
        } catch PortReaderError.emptyFile {
            // Expected
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }

    @Test("throws on non-numeric content")
    func throwsOnNonNumeric() throws {
        let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let portFile = tmpDir.appendingPathComponent("daemon.port")
        try "not-a-port".write(to: portFile, atomically: true, encoding: .utf8)

        let reader = PortReader()
        do {
            _ = try reader.readPort(from: portFile)
            Issue.record("Expected error for non-numeric content")
        } catch PortReaderError.invalidPort {
            // Expected
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }

    @Test("throws on out-of-range port")
    func throwsOnOutOfRangePort() throws {
        let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let portFile = tmpDir.appendingPathComponent("daemon.port")
        try "99999\n".write(to: portFile, atomically: true, encoding: .utf8)

        let reader = PortReader()
        do {
            _ = try reader.readPort(from: portFile)
            Issue.record("Expected error for out of range port")
        } catch PortReaderError.invalidPort {
            // Expected
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }

    @Test("throws when file does not exist")
    func throwsOnMissingFile() throws {
        let nonExistent = URL(fileURLWithPath: "/tmp/hr-test-no-such-file-\(UUID().uuidString)")
        let reader = PortReader()
        do {
            _ = try reader.readPort(from: nonExistent)
            Issue.record("Expected error for missing file")
        } catch PortReaderError.fileNotFound {
            // Expected
        } catch {
            Issue.record("Unexpected error: \(error)")
        }
    }

    @Test("reads URL string from port")
    func readsURLFromPort() throws {
        let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent("hr-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmpDir) }

        let portFile = tmpDir.appendingPathComponent("daemon.port")
        try "4321\n".write(to: portFile, atomically: true, encoding: .utf8)

        let reader = PortReader()
        let url = try reader.readLocalURL(from: portFile)
        #expect(url == "http://127.0.0.1:4321/")
    }
}
