import Foundation

/// Errors thrown by PortReader.
public enum PortReaderError: Error, Equatable {
    case fileNotFound
    case emptyFile
    case invalidPort
}

/// Reads the daemon port from a state file at ~/.happyranch/daemon.port.
public struct PortReader: Sendable {

    public init() {}

    /// Reads a port number from the given file.
    /// The file is expected to contain a single integer in the valid port range (1-65535).
    public func readPort(from fileURL: URL) throws -> UInt16 {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            throw PortReaderError.fileNotFound
        }

        let content = try String(contentsOf: fileURL, encoding: .utf8)
        let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !trimmed.isEmpty else {
            throw PortReaderError.emptyFile
        }

        guard let port = UInt16(trimmed), port > 0 else {
            throw PortReaderError.invalidPort
        }

        return port
    }

    /// Reads the port file at daemon_home and returns the local web URL.
    /// Always binds to 127.0.0.1 (loopback only — hard constraint).
    public func readLocalURL(from portFile: URL) throws -> String {
        let port = try readPort(from: portFile)
        return "http://127.0.0.1:\(port)/"
    }
}
